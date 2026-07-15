"""Opt-in measurements for strict JSONL scan complexity and resource bounds."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from polaris.kernelone.events.tests.strict_scan_performance import (
    run_strict_scan_performance_harness,
)


def _env_positive_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise pytest.UsageError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise pytest.UsageError(f"{name} must be >= 1")
    return parsed


def test_strict_scan_performance_harness_is_opt_in_and_bounded(tmp_path: Path) -> None:
    if os.environ.get("POLARIS_STRICT_SCAN_PERF") != "1":
        pytest.skip("set POLARIS_STRICT_SCAN_PERF=1 to run strict scan measurements")
    metrics = run_strict_scan_performance_harness(
        tmp_path / "workspace",
        event_count=_env_positive_int("POLARIS_STRICT_SCAN_PERF_EVENTS", 320),
        repeat_count=_env_positive_int("POLARIS_STRICT_SCAN_PERF_REPEATS", 9),
    )

    assert metrics.event_count <= metrics.strict_record_cap == 320
    assert metrics.stream_bytes <= metrics.strict_byte_cap == 2 * 1024 * 1024
    assert metrics.strict_p50_ms >= 0
    assert metrics.strict_p95_ms >= metrics.strict_p50_ms
    assert metrics.strict_p99_ms >= metrics.strict_p95_ms
    assert metrics.lenient_p50_ms >= 0
    assert metrics.approximate_incremental_memory_bytes >= 0
