"""Opt-in performance harness for the strict JSONL event decoder."""

from __future__ import annotations

import statistics
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

from polaris.kernelone.events.sourcing import JsonlEventStore

_MAX_EVENTS = 320
_MAX_STREAM_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StrictScanPerformanceMetrics:
    event_count: int
    repeat_count: int
    strict_p50_ms: float
    strict_p95_ms: float
    strict_p99_ms: float
    lenient_p50_ms: float
    approximate_incremental_memory_bytes: int
    stream_bytes: int
    strict_record_cap: int
    strict_byte_cap: int


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("samples must not be empty")
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run_strict_scan_performance_harness(
    workspace: Path,
    *,
    event_count: int = _MAX_EVENTS,
    repeat_count: int = 9,
) -> StrictScanPerformanceMetrics:
    """Measure bounded strict scans without imposing a wall-clock pass/fail gate."""

    if type(event_count) is not int or not 1 <= event_count <= _MAX_EVENTS:
        raise ValueError(f"event_count must be an exact integer in [1, {_MAX_EVENTS}]")
    if type(repeat_count) is not int or repeat_count < 1:
        raise ValueError("repeat_count must be an exact positive integer")
    store = JsonlEventStore(
        str(workspace),
        strict_max_records=_MAX_EVENTS,
        strict_max_bytes=_MAX_STREAM_BYTES,
    )
    stream = "strict.performance"
    for index in range(event_count):
        store.append(
            stream=stream,
            event_type="recorded",
            source="strict.performance",
            payload={"index": index, "nested": {"ok": True}},
            strict_integrity=True,
        )

    strict_samples: list[float] = []
    lenient_samples: list[float] = []
    tracemalloc.start()
    try:
        for _ in range(repeat_count):
            started = time.perf_counter()
            strict_result = store.query(stream=stream, limit=event_count, strict_integrity=True)
            strict_samples.append((time.perf_counter() - started) * 1000)
            assert strict_result.total == event_count
        _, strict_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        for _ in range(repeat_count):
            started = time.perf_counter()
            lenient_result = store.query(stream=stream, limit=event_count)
            lenient_samples.append((time.perf_counter() - started) * 1000)
            assert lenient_result.total == event_count
    finally:
        tracemalloc.stop()
    stream_path = store._kernel_fs.resolve_path(store.stream_logical_path(stream))
    return StrictScanPerformanceMetrics(
        event_count=event_count,
        repeat_count=repeat_count,
        strict_p50_ms=_percentile(strict_samples, 0.50),
        strict_p95_ms=_percentile(strict_samples, 0.95),
        strict_p99_ms=_percentile(strict_samples, 0.99),
        lenient_p50_ms=statistics.median(lenient_samples),
        approximate_incremental_memory_bytes=strict_peak,
        stream_bytes=stream_path.stat().st_size,
        strict_record_cap=_MAX_EVENTS,
        strict_byte_cap=_MAX_STREAM_BYTES,
    )
