"""OpenTelemetry/Jaeger Exporter — 导出TracingAuditInterceptor span到外部追踪系统.

Features:
- OTLP HTTP Protocol (无外部依赖，纯HTTP POST)
- Jaeger_THRIFT 格式兼容
- Batch span导出
- 可配置采样率

Usage:
    # OTLP Exporter
    exporter = OpenTelemetryExporter(
        endpoint="http://localhost:4318/v1/traces",
        service_name="polaris-audit",
    )
    await exporter.export_span(span_data)

    # Jaeger Exporter
    jaeger = JaegerExporter(
        endpoint="http://localhost:14268/api/traces",
        service_name="polaris-audit",
    )
    await jaeger.export_span(span_data)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_ENDPOINT", "http://localhost:4318/v1/traces")
DEFAULT_JAEGER_ENDPOINT = os.environ.get("JAEGER_ENDPOINT", "http://localhost:14268/api/traces")
DEFAULT_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "polaris-audit")


class OpenTelemetryExporter:
    """OpenTelemetry span exporter via OTLP HTTP Protocol.

    Sends spans to any OTLP-compatible backend (Grafana Tempo,
    Jaeger with OTLP support, etc.) using JSON-encoded OTLP.

    Attributes:
        endpoint: OTLP HTTP receiver URL.
        service_name: Service name for all spans.
        max_batch_size: Max spans per batch (default 100).
        timeout_seconds: Request timeout.
        sampling_rate: Sampling rate 0.0-1.0 (1.0 = 100%).
    """

    def __init__(
        self,
        endpoint: str | None = None,
        service_name: str | None = None,
        max_batch_size: int = 100,
        timeout_seconds: float = 5.0,
        sampling_rate: float = 1.0,
    ) -> None:
        """Initialize the OTLP exporter.

        Args:
            endpoint: OTLP HTTP endpoint URL.
            service_name: Service name for spans.
            max_batch_size: Maximum spans in a batch.
            timeout_seconds: HTTP request timeout.
            sampling_rate: Sampling rate (0.0-1.0).
        """
        self._endpoint = endpoint or DEFAULT_OTEL_ENDPOINT
        self._service_name = service_name or DEFAULT_SERVICE_NAME
        self._max_batch_size = max_batch_size
        self._timeout_seconds = timeout_seconds
        self._sampling_rate = max(0.0, min(1.0, sampling_rate))

        # Span batch buffer
        self._batch: list[dict[str, Any]] = []
        self._batch_lock = asyncio.Lock()
        self._export_task: asyncio.Task[None] | None = None

        # Statistics
        self._spans_exported = 0
        self._spans_dropped = 0
        self._export_errors = 0

    async def export_span(self, span_data: dict[str, Any]) -> bool:
        """Export a single span.

        Args:
            span_data: Span dict from TracingAuditInterceptor. Expected keys:
                - name: span name
                - trace_id: trace ID (hex)
                - span_id: span ID (hex)
                - parent_span_id: parent span ID (hex, optional)
                - start_time: ISO8601 timestamp
                - end_time: ISO8601 timestamp
                - attributes: dict of span attributes
                - status: "ok" or "error"
                - events: list of span events (optional)

        Returns:
            True if span was queued for export.
        """
        # Sampling check
        if random.random() > self._sampling_rate:
            self._spans_dropped += 1
            return False

        # Convert to OTLP resource span format
        otlp_span = self._to_otlp_span(span_data)

        async with self._batch_lock:
            self._batch.append(otlp_span)
            batch_full = len(self._batch) >= self._max_batch_size

        if batch_full:
            await self._flush_batch()

        return True

    async def export_batch(self, spans: list[dict[str, Any]]) -> int:
        """Export multiple spans.

        Args:
            spans: List of span dicts.

        Returns:
            Number of spans queued.
        """
        count = 0
        for span in spans:
            if await self.export_span(span):
                count += 1
        return count

    def _to_otlp_span(self, span_data: dict[str, Any]) -> dict[str, Any]:
        """Convert TracingAuditInterceptor span to OTLP JSON format.

        Args:
            span_data: Raw span dict.

        Returns:
            OTLP-compatible span dict.
        """
        # Parse timestamps
        start_time = span_data.get("start_time", "")
        end_time = span_data.get("end_time", "")

        if isinstance(start_time, str):
            try:
                start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except ValueError:
                start_time = datetime.now(timezone.utc)
        if isinstance(end_time, str):
            try:
                end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            except ValueError:
                end_time = datetime.now(timezone.utc)

        # Convert attributes
        attrs = span_data.get("attributes", {})
        if isinstance(attrs, dict):
            # OTLP expects key-value pairs as list of KeyValue
            otlp_attrs = []
            for k, v in attrs.items():
                if isinstance(v, bool):
                    otlp_attrs.append({"key": k, "value": {"boolValue": v}})
                elif isinstance(v, int):
                    otlp_attrs.append({"key": k, "value": {"intValue": v}})
                elif isinstance(v, float):
                    otlp_attrs.append({"key": k, "value": {"doubleValue": v}})
                else:
                    otlp_attrs.append({"key": k, "value": {"stringValue": str(v)}})
        else:
            otlp_attrs = []

        # Trace ID / Span ID (must be 16/8 bytes hex)
        trace_id = span_data.get("trace_id", "")
        span_id = span_data.get("span_id", "")
        parent_span_id = span_data.get("parent_span_id", "")

        # Build OTLP span
        otlp_span = {
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id,
            "name": span_data.get("name", "unknown"),
            "kind": 1,  # SPAN_KIND_INTERNAL = 1
            "startTimeUnixNano": str(int(start_time.timestamp() * 1e9)) if start_time else "0",
            "endTimeUnixNano": str(int(end_time.timestamp() * 1e9)) if end_time else "0",
            "attributes": otlp_attrs,
            "status": {"code": 1 if span_data.get("status") == "ok" else 2},  # 1=OK, 2=ERROR
        }

        # Add events (span events as OTLP events)
        events = span_data.get("events", [])
        if events:
            otlp_span["events"] = [
                {
                    "timeUnixNano": str(int(datetime.now(timezone.utc).timestamp() * 1e9)),
                    "name": e.get("name", ""),
                    "attributes": [
                        {"key": k, "value": {"stringValue": str(v)}}
                        for k, v in (e.get("attributes", {}).items() if isinstance(e, dict) else {})
                    ],
                }
                for e in events
            ]

        return otlp_span

    def _build_otlp_payload(self, spans: list[dict[str, Any]]) -> bytes:
        """Build OTLP JSON payload for a batch of spans.

        Args:
            spans: List of OTLP span dicts.

        Returns:
            JSON bytes.
        """
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self._service_name}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "polaris-audit",
                                "version": "1.0.0",
                            },
                            "spans": spans,
                        }
                    ],
                }
            ]
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def _flush_batch(self) -> None:
        """Flush the current batch to the OTLP endpoint."""
        async with self._batch_lock:
            if not self._batch:
                return
            batch = list(self._batch)
            self._batch.clear()

        try:
            await self._send_batch(batch)
            self._spans_exported += len(batch)
        except (RuntimeError, ValueError) as exc:
            self._spans_dropped += len(batch)
            self._export_errors += 1
            logger.warning("[otel_exporter] Failed to export batch: %s", exc)

    async def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        """Send a batch of spans via HTTP POST.

        Args:
            batch: List of OTLP span dicts.
        """
        payload = self._build_otlp_payload(batch)

        loop = asyncio.get_event_loop()

        def _sync_post() -> None:
            req = urllib.request.Request(
                self._endpoint,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "polaris-audit/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout_seconds):  # type: ignore[arg-type]
                pass  # Context manager handles connection close; response body is irrelevant

        await loop.run_in_executor(None, _sync_post)

    async def flush(self) -> None:
        """Flush any pending spans."""
        await self._flush_batch()

    def get_stats(self) -> dict[str, Any]:
        """Get exporter statistics.

        Returns:
            Dictionary with export counts and errors.
        """
        return {
            "spans_exported": self._spans_exported,
            "spans_dropped": self._spans_dropped,
            "export_errors": self._export_errors,
            "pending_in_batch": len(self._batch),
            "service_name": self._service_name,
            "endpoint": self._endpoint,
        }


class JaegerExporter(OpenTelemetryExporter):
    """Jaeger trace exporter via Jaeger HTTP Thrift API.

    Extends OpenTelemetryExporter to output Jaeger-compatible
    span format (Jaeger_THRIFT via HTTP).

    Usage:
        exporter = JaegerExporter(
            endpoint="http://localhost:14268/api/traces",
            service_name="polaris-audit",
        )
        await exporter.export_span(span_data)
    """

    def __init__(
        self,
        endpoint: str | None = None,
        service_name: str | None = None,
        max_batch_size: int = 100,
        timeout_seconds: float = 5.0,
        sampling_rate: float = 1.0,
    ) -> None:
        """Initialize the Jaeger exporter.

        Args:
            endpoint: Jaeger HTTP endpoint (default: JAEGER_ENDPOINT env).
            service_name: Service name for spans.
            max_batch_size: Max spans per batch.
            timeout_seconds: HTTP timeout.
            sampling_rate: Sampling rate.
        """
        super().__init__(
            endpoint=endpoint or DEFAULT_JAEGER_ENDPOINT,
            service_name=service_name or DEFAULT_SERVICE_NAME,
            max_batch_size=max_batch_size,
            timeout_seconds=timeout_seconds,
            sampling_rate=sampling_rate,
        )
        self._jaeger_endpoint = self._endpoint

    def _to_jaeger_span(self, span_data: dict[str, Any]) -> dict[str, Any]:
        """Convert a span dict to Jaeger Thrift format.

        Args:
            span_data: Raw span dict.

        Returns:
            Jaeger-compatible span dict.
        """
        # Parse timestamps
        start_time = span_data.get("start_time", "")
        end_time = span_data.get("end_time", "")

        if isinstance(start_time, str):
            try:
                start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except ValueError:
                start_time = datetime.now(timezone.utc)
        if isinstance(end_time, str):
            try:
                end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            except ValueError:
                end_time = datetime.now(timezone.utc)

        # Duration in microseconds
        duration_us = 0
        if start_time and end_time:
            duration_us = int((end_time - start_time).total_seconds() * 1e6)

        # Parse trace/span IDs
        trace_id = span_data.get("trace_id", "")
        span_id = span_data.get("span_id", "")
        parent_span_id = span_data.get("parent_span_id", "")

        # Jaeger expects traceId as 32 hex chars, spanId as 16 hex chars
        # Pad if needed
        trace_id = trace_id.ljust(32, "0")[:32]
        span_id = span_id.ljust(16, "0")[:16]
        if parent_span_id:
            parent_span_id = parent_span_id.ljust(16, "0")[:16]

        # Build tags (Jaeger uses tags instead of attributes)
        tags = []
        attrs = span_data.get("attributes", {})
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                tag = {"key": k}
                if isinstance(v, str):
                    tag["vType"] = "STRING"
                    tag["vStr"] = v
                elif isinstance(v, bool):
                    tag["vType"] = "BOOL"
                    tag["vBool"] = v
                elif isinstance(v, int):
                    tag["vType"] = "INT64"
                    tag["vInt64"] = v
                elif isinstance(v, float):
                    tag["vType"] = "FLOAT64"
                    tag["vDouble"] = v
                else:
                    tag["vType"] = "STRING"
                    tag["vStr"] = str(v)
                tags.append(tag)

        # Status tag
        status = span_data.get("status", "ok")
        tags.append({"key": "otel.status_code", "vType": "STRING", "vStr": "OK" if status == "ok" else "ERROR"})
        if status == "error":
            error_msg = attrs.get("error.message", "") if isinstance(attrs, dict) else ""
            tags.append({"key": "error", "vType": "BOOL", "vBool": True})
            if error_msg:
                tags.append({"key": "error.message", "vType": "STRING", "vStr": error_msg})

        jaeger_span = {
            "traceId": trace_id,
            "spanId": span_id,
            "parentSpanId": parent_span_id or "0000000000000000",
            "operationName": span_data.get("name", "unknown"),
            "flags": 1,  # Active trace
            "startTime": int(start_time.timestamp() * 1e6) if start_time else 0,
            "duration": duration_us,
            "tags": tags,
        }

        # Add logs (span events)
        events = span_data.get("events", [])
        if events:
            jaeger_span["logs"] = [
                {
                    "timestamp": int(datetime.now(timezone.utc).timestamp() * 1e6),
                    "fields": [
                        {"key": e.get("name", "event"), "vType": "STRING", "vStr": json.dumps(e.get("attributes", {}))}
                        if isinstance(e, dict)
                        else {"key": "event", "vType": "STRING", "vStr": str(e)}
                        for e in events
                    ],
                }
            ]

        return jaeger_span

    def _build_jaeger_payload(self, spans: list[dict[str, Any]]) -> bytes:
        """Build Jaeger HTTP Thrift batch payload.

        Args:
            spans: List of Jaeger span dicts.

        Returns:
            JSON bytes.
        """
        batch = {
            "batch": {
                "process": {
                    "serviceName": self._service_name,
                    "tags": [
                        {"key": "service.name", "vType": "STRING", "vStr": self._service_name},
                    ],
                },
                "spans": spans,
            }
        }
        return json.dumps(batch, ensure_ascii=False).encode("utf-8")

    async def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        """Send batch to Jaeger HTTP endpoint."""
        # Convert to Jaeger format
        jaeger_spans = [self._to_jaeger_span(span) for span in batch]
        payload = self._build_jaeger_payload(jaeger_spans)

        loop = asyncio.get_event_loop()

        def _sync_post() -> None:
            req = urllib.request.Request(
                self._jaeger_endpoint,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "polaris-audit/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout_seconds):  # type: ignore[arg-type]
                pass  # Context manager handles connection close

        await loop.run_in_executor(None, _sync_post)


__all__ = [
    "DEFAULT_JAEGER_ENDPOINT",
    "DEFAULT_OTEL_ENDPOINT",
    "DEFAULT_SERVICE_NAME",
    "JaegerExporter",
    "OpenTelemetryExporter",
]
